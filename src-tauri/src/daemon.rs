use crate::{
    config::{load_daemon_status, save_daemon_status, AppPaths},
    models::CompanionSettings,
};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};
use tauri::{path::BaseDirectory, Manager, Runtime};

pub fn install_and_start<R: Runtime, M: Manager<R>>(
    app: &M,
    settings: &CompanionSettings,
) -> Result<(), String> {
    let paths = AppPaths::new(app)?;
    let mut status = load_daemon_status(app)?;
    status.state = "installing".into();
    status.last_error = None;
    save_daemon_status(app, &status)?;

    let command = install_daemon_payload(app, &paths)?;
    write_service_definition(&paths, &command)?;
    start_service(&paths, &command)?;

    status.state = "registering".into();
    status.registered = false;
    status.last_error = None;
    status.public_key_path = Some(paths.public_key_path.display().to_string());
    status.service_mode = crate::models::current_service_mode();
    save_daemon_status(app, &status)?;

    let _ = settings;
    Ok(())
}

enum InstalledCommand {
    Binary(PathBuf),
    PythonScript {
        interpreter: String,
        script: PathBuf,
    },
}

impl InstalledCommand {
    #[cfg_attr(target_os = "windows", allow(dead_code))]
    fn arguments(&self, config_path: &Path) -> Vec<String> {
        match self {
            Self::Binary(binary) => vec![
                binary.display().to_string(),
                "--config".into(),
                config_path.display().to_string(),
            ],
            Self::PythonScript {
                interpreter,
                script,
            } => vec![
                interpreter.clone(),
                script.display().to_string(),
                "--config".into(),
                config_path.display().to_string(),
            ],
        }
    }

    #[cfg(target_os = "windows")]
    fn windows_launch_command(&self, config_path: &Path) -> Result<WindowsLaunchCommand, String> {
        match self {
            Self::Binary(binary) => Ok(WindowsLaunchCommand {
                command_line: format!(
                    "{} --config {}",
                    windows_argument(&binary.display().to_string()),
                    windows_argument(&config_path.display().to_string())
                ),
                working_directory: binary
                    .parent()
                    .unwrap_or(config_path.parent().unwrap_or(Path::new(".")))
                    .display()
                    .to_string(),
            }),
            Self::PythonScript {
                interpreter,
                script,
            } => Ok(WindowsLaunchCommand {
                command_line: format!(
                    "{} {} --config {}",
                    windows_argument(
                        &resolve_windows_command(interpreter)
                            .unwrap_or_else(|| interpreter.to_string())
                    ),
                    windows_argument(&script.display().to_string()),
                    windows_argument(&config_path.display().to_string())
                ),
                working_directory: script
                    .parent()
                    .unwrap_or(config_path.parent().unwrap_or(Path::new(".")))
                    .display()
                    .to_string(),
            }),
        }
    }

    #[cfg(target_os = "macos")]
    fn plist_arguments(&self, config_path: &Path) -> Vec<String> {
        self.arguments(config_path)
    }
}

fn install_daemon_payload<R: Runtime, M: Manager<R>>(
    app: &M,
    paths: &AppPaths,
) -> Result<InstalledCommand, String> {
    if let Some(packaged_binary) = resolve_packaged_binary(app) {
        fs::copy(&packaged_binary, &paths.daemon_binary_path).map_err(|error| error.to_string())?;
        make_executable(&paths.daemon_binary_path)?;
        return Ok(InstalledCommand::Binary(paths.daemon_binary_path.clone()));
    }

    let repo_script = std::env::current_dir()
        .map_err(|error| error.to_string())?
        .join("daemon")
        .join("hermes-node-daemon.py");

    if repo_script.exists() {
        fs::copy(&repo_script, &paths.daemon_script_path).map_err(|error| error.to_string())?;
        make_executable(&paths.daemon_script_path)?;

        let interpreter = if cfg!(target_os = "windows") {
            "python".into()
        } else {
            "python3".into()
        };

        return Ok(InstalledCommand::PythonScript {
            interpreter,
            script: paths.daemon_script_path.clone(),
        });
    }

    Err("No daemon payload was found. Run a platform build script so the packaged daemon is bundled into src-tauri/resources/daemon.".into())
}

fn resolve_packaged_binary<R: Runtime, M: Manager<R>>(app: &M) -> Option<PathBuf> {
    let candidates = [
        format!(
            "daemon/{}",
            if cfg!(target_os = "windows") {
                "hermes-node-daemon.exe"
            } else {
                "hermes-node-daemon"
            }
        ),
        "daemon/hermes-node-daemon.exe".into(),
        "daemon/hermes-node-daemon".into(),
    ];

    for candidate in candidates {
        if let Ok(resolved) = app.path().resolve(candidate, BaseDirectory::Resource) {
            if resolved.exists() {
                return Some(resolved);
            }
        }
    }

    None
}

fn write_service_definition(paths: &AppPaths, command: &InstalledCommand) -> Result<(), String> {
    #[cfg(target_os = "linux")]
    {
        let exec_start = command
            .arguments(&paths.daemon_config_path)
            .into_iter()
            .map(|value| format!("\"{}\"", value.replace('"', "\\\"")))
            .collect::<Vec<_>>()
            .join(" ");

        let service = format!(
      "[Unit]\nDescription=Hermes Companion Node Daemon\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nExecStart={exec_start}\nWorkingDirectory={workdir}\nRestart=always\nRestartSec=5\nNoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=read-only\nReadWritePaths={runtime_root}\n\n[Install]\nWantedBy=default.target\n",
      workdir = paths.root.display(),
      runtime_root = paths.root.display(),
    );

        fs::write(&paths.service_definition_path, service).map_err(|error| error.to_string())?;
    }

    #[cfg(target_os = "macos")]
    {
        let arguments = command
            .plist_arguments(&paths.daemon_config_path)
            .into_iter()
            .map(|value| format!("    <string>{}</string>", xml_escape(&value)))
            .collect::<Vec<_>>()
            .join("\n");

        let plist = format!(
      "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"https://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\">\n<dict>\n  <key>Label</key>\n  <string>com.hermes.node.daemon</string>\n  <key>ProgramArguments</key>\n  <array>\n{arguments}\n  </array>\n  <key>KeepAlive</key>\n  <true/>\n  <key>RunAtLoad</key>\n  <true/>\n  <key>StandardOutPath</key>\n  <string>{stdout_path}</string>\n  <key>StandardErrorPath</key>\n  <string>{stderr_path}</string>\n</dict>\n</plist>\n",
      stdout_path = xml_escape(&paths.log_path.display().to_string()),
      stderr_path = xml_escape(&paths.log_path.display().to_string()),
    );

        fs::write(&paths.service_definition_path, plist).map_err(|error| error.to_string())?;
    }

    #[cfg(target_os = "windows")]
    {
        let script = build_windows_service_definition(command, paths)?;

        fs::write(&paths.service_definition_path, script).map_err(|error| error.to_string())?;
    }

    Ok(())
}

fn start_service(paths: &AppPaths, _command: &InstalledCommand) -> Result<(), String> {
    #[cfg(target_os = "linux")]
    {
        let user_dir = std::env::var("HOME")
            .map(PathBuf::from)
            .map_err(|error| error.to_string())?
            .join(".config/systemd/user");
        fs::create_dir_all(&user_dir).map_err(|error| error.to_string())?;
        let unit_path = user_dir.join("hermes-node-daemon.service");
        fs::copy(&paths.service_definition_path, &unit_path).map_err(|error| error.to_string())?;
        run_command("systemctl", &["--user", "daemon-reload"])?;
        run_command(
            "systemctl",
            &["--user", "enable", "--now", "hermes-node-daemon.service"],
        )?;
    }

    #[cfg(target_os = "macos")]
    {
        let launch_agents = std::env::var("HOME")
            .map(PathBuf::from)
            .map_err(|error| error.to_string())?
            .join("Library/LaunchAgents");
        fs::create_dir_all(&launch_agents).map_err(|error| error.to_string())?;
        let plist_path = launch_agents.join("com.hermes.node.daemon.plist");
        fs::copy(&paths.service_definition_path, &plist_path).map_err(|error| error.to_string())?;
        let _ = Command::new("launchctl")
            .arg("unload")
            .arg(&plist_path)
            .status();
        run_command(
            "launchctl",
            &["load", "-w", &plist_path.display().to_string()],
        )?;
    }

    #[cfg(target_os = "windows")]
    {
        let startup_dir = windows_startup_dir()?;
        fs::create_dir_all(&startup_dir).map_err(|error| error.to_string())?;
        let startup_launcher = startup_dir.join("Hermes Companion Daemon.vbs");
        fs::copy(&paths.service_definition_path, &startup_launcher)
            .map_err(|error| error.to_string())?;
        cleanup_legacy_windows_task();
        run_command("wscript", &[&startup_launcher.display().to_string()])?;
    }

    Ok(())
}

fn make_executable(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o755))
            .map_err(|error| error.to_string())?;
    }

    #[cfg(not(unix))]
    {
        let _ = path;
    }

    Ok(())
}

fn run_command(program: &str, args: &[&str]) -> Result<(), String> {
    let output = Command::new(program)
        .args(args)
        .output()
        .map_err(|error| format!("Failed to launch {program}: {error}"))?;

    if output.status.success() {
        return Ok(());
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);
    Err(format!(
        "{program} exited with {}.\n{}\n{}",
        output.status,
        stdout.trim(),
        stderr.trim()
    ))
}

#[cfg(target_os = "macos")]
fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

#[cfg(target_os = "windows")]
struct WindowsLaunchCommand {
    command_line: String,
    working_directory: String,
}

#[cfg(target_os = "windows")]
fn build_windows_service_definition(
    command: &InstalledCommand,
    paths: &AppPaths,
) -> Result<String, String> {
    let launcher = command.windows_launch_command(&paths.daemon_config_path)?;

    Ok(format!(
        "Set shell = CreateObject(\"WScript.Shell\")\r\n\
         shell.CurrentDirectory = {working_directory}\r\n\
         shell.Run {command_line}, 0, False\r\n",
        working_directory = vbs_string_literal(&launcher.working_directory),
        command_line = vbs_string_literal(&launcher.command_line),
    ))
}

#[cfg(target_os = "windows")]
fn resolve_windows_command(command: &str) -> Option<String> {
    let output = Command::new("where").arg(command).output().ok()?;
    if !output.status.success() {
        return None;
    }

    String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(|line| line.to_string())
}

#[cfg(target_os = "windows")]
fn windows_argument(value: &str) -> String {
    if value.is_empty() {
        return "\"\"".into();
    }

    if value.contains([' ', '\t', '"']) {
        format!("\"{}\"", value.replace('"', "\\\""))
    } else {
        value.to_string()
    }
}

#[cfg(target_os = "windows")]
fn vbs_string_literal(value: &str) -> String {
    format!("\"{}\"", value.replace('"', "\"\""))
}

#[cfg(target_os = "windows")]
fn windows_startup_dir() -> Result<PathBuf, String> {
    let app_data = std::env::var("APPDATA").map_err(|error| error.to_string())?;
    Ok(PathBuf::from(app_data).join("Microsoft\\Windows\\Start Menu\\Programs\\Startup"))
}

#[cfg(target_os = "windows")]
fn cleanup_legacy_windows_task() {
    let _ = Command::new("schtasks")
        .args(["/Delete", "/TN", "HermesCompanionDaemon", "/F"])
        .output();
}

#[cfg(all(test, target_os = "windows"))]
mod tests {
    use super::*;

    #[test]
    fn windows_service_definition_uses_hidden_startup_launcher() {
        let command = InstalledCommand::Binary(PathBuf::from(
            r"C:\Program Files\Hermes Companion\daemon\bin\hermes-node-daemon.exe",
        ));
        let paths = AppPaths {
            root: PathBuf::from(r"C:\Users\Test\AppData\Roaming\com.hermes.companion"),
            config_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\client-config.json",
            ),
            daemon_root: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\daemon",
            ),
            daemon_bin_dir: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\daemon\bin",
            ),
            daemon_binary_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\daemon\bin\hermes-node-daemon.exe",
            ),
            daemon_script_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\daemon\bin\hermes-node-daemon.py",
            ),
            daemon_config_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\daemon\daemon-config.json",
            ),
            state_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\daemon\daemon-state.json",
            ),
            keys_dir: PathBuf::from(r"C:\Users\Test\AppData\Roaming\com.hermes.companion\keys"),
            private_key_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\keys\hermes_node_ed25519",
            ),
            public_key_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\keys\hermes_node_ed25519.pub",
            ),
            status_dir: PathBuf::from(r"C:\Users\Test\AppData\Roaming\com.hermes.companion\status"),
            status_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\status\node-status.json",
            ),
            logs_dir: PathBuf::from(r"C:\Users\Test\AppData\Roaming\com.hermes.companion\logs"),
            log_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\logs\hermes-node-daemon.log",
            ),
            service_definition_path: PathBuf::from(
                r"C:\Users\Test\AppData\Roaming\com.hermes.companion\daemon\start-windows-daemon.vbs",
            ),
        };

        let script = build_windows_service_definition(&command, &paths).expect("script");
        assert!(script.contains("CreateObject(\"WScript.Shell\")"));
        assert!(script.contains("shell.CurrentDirectory"));
        assert!(script.contains("shell.Run"));
        assert!(!script.contains("cmd.exe"));
        assert!(!script.contains("schtasks"));
        assert!(script.contains(
            "\"\"\"C:\\Program Files\\Hermes Companion\\daemon\\bin\\hermes-node-daemon.exe\"\" --config"
        ));
        assert!(script.contains(
            "C:\\Users\\Test\\AppData\\Roaming\\com.hermes.companion\\daemon\\daemon-config.json"
        ));
    }
}
